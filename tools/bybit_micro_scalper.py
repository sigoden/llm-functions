#!/usr/bin/env python3
import hashlib
import hmac
import json
import os
import sys
import time

import requests

# @meta title Bybit Micro-Profit Scalper
# @meta description High-frequency scalping tool optimized for tiny $0.02 to $0.20 net profit targets using orderbook and momentum conditions.
# @env BYBIT_API_KEY The Bybit API key.
# @env BYBIT_API_SECRET The Bybit API secret.
# @option --symbol=BTCUSDT The target crypto derivative trading pair.
# @option --qty=0.01 The order size/quantity defined in the base asset.
# @option --target-profit=0.05 The desired micro-profit target in USDT (e.g., between 0.02 and 0.20).
# @option --maker-fee=0.0002 The account maker trading fee rate tier (default is 0.02% for VIP0).
# @option --trailing-stop! <pct>  Enable a trailing‑stop that trails the entry price by <pct>% (e.g., 0.5 for 0.5%).
# @option --balance-check!      Perform a pre‑flight free‑margin check before sending the order.
BASE_URL = "https://bybit.com"


def generate_signature(secret, timestamp, api_key, recv_window, payload):
    param_str = str(timestamp) + api_key + str(recv_window) + payload
    return hmac.new(
        secret.encode("utf-8"), param_str.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def send_signed_post(endpoint, payload_dict, api_key, api_secret):
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


def get_market_data(symbol):
    # UPGRADE 1: cache‑busting headers
    session = requests.Session()
    session.headers.update({"Cache-Control": "no-cache", "Pragma": "no-cache"})

    # Order book
    ob_url = f"{BASE_URL}/v5/market/orderbook?category=linear&symbol={symbol}&limit=5"
    ob_data = session.get(ob_url).json()["result"]
    best_bid, best_ask = float(ob_data["b"]), float(ob_data["a"])

    bid_vol = sum([float(b) for b in ob_data["b"]])
    ask_vol = sum([float(a) for a in ob_data["a"]])
    imbalance = (
        (bid_vol - ask_vol) / (bid_vol + ask_vol) if (bid_vol + ask_vol) > 0 else 0
    )

    # Candles for micro‑momentum
    kline_url = (
        f"{BASE_URL}/v5/market/kline?category=linear&symbol={symbol}&interval=1&limit=3"
    )
    kline_list = session.get(kline_url).json()["result"]["list"]
    close_now, close_past = float(kline_list[-1]), float(kline_list[0])
    momentum = (close_now - close_past) / close_past if close_past > 0 else 0

    # Tick size
    instrument_url = (
        f"{BASE_URL}/v5/market/instruments-info?category=linear&symbol={symbol}"
    )
    inst_data = session.get(instrument_url).json()["result"]["list"]
    tick_size = float(inst_data["priceFilter"]["tickSize"])

    return best_bid, best_ask, imbalance, momentum, tick_size


def round_to_tick(price, tick_size):
    return round(round(price / tick_size) * tick_size, 8)


def check_margin(api_key, api_secret, symbol, qty, entry_price, maker_fee):
    """
    Calls /v5/account/getbalance and verifies that free cross‑wallet balance
    covers the required margin for the proposed entry.
    """
    endpoint = "/v5/account/getbalance"
    payload = {"accountType": "UNIFIED", "login": api_key}
    resp = send_signed_post(endpoint, payload, api_key, api_secret)
    if resp.get("retCode") != 0:
        raise RuntimeError(f"Balance‑check failed: {resp}")

    balances = resp.get("result", {}).get("list", [])
    total_balance = float(next(b["totalCrossWalletBalance"] for b in balances))
    required = qty * entry_price * (1 + maker_fee)
    if total_balance < required:
        raise RuntimeError(
            f"Insufficient margin: required ${required:.8f} USDT, "
            f"available ${total_balance:.8f}"
        )
    return total_balance


def main(args):
    # ----------------------------------------------------------------------
    # 1️⃣  Parse CLI arguments (argc‑compatible)
    # ----------------------------------------------------------------------
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
    trailing_stop_pct = args.get("--trailing-stop")  # may be None
    balance_check = "--balance-check" in args  # boolean flag

    # ----------------------------------------------------------------------
    # 2️⃣  Basic validation
    # ----------------------------------------------------------------------
    if target_profit < 0.01 or target_profit > 1.00:
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": "Target profit must be within micro‑bounds ($0.01 to $1.00 USDT)",
                }
            )
        )
        return

    # ----------------------------------------------------------------------
    # 3️⃣  Market data fetch
    # ----------------------------------------------------------------------
    try:
        bid, ask, imbalance, momentum, tick_size = get_market_data(symbol)
    except Exception as e:
        print(
            json.dumps(
                {"status": "error", "message": f"Market data fetch failed: {e!s}"}
            )
        )
        return

    side, entry_price, exit_price = None, 0.0, 0.0

    # ----------------------------------------------------------------------
    # 4️⃣  Smart liquidity positioning (unchanged logic)
    # ----------------------------------------------------------------------
    if momentum > 0.0001 and imbalance > 0.05:
        side = "Buy"
        entry_price = bid
        entry_fee = entry_price * qty * maker_fee
        raw_exit = (target_profit + entry_fee + (entry_price * qty)) / (
            qty * (1 - maker_fee)
        )
        exit_price = round_to_tick(raw_exit, tick_size)
    elif momentum < -0.0001 and imbalance < -0.05:
        side = "Sell"
        entry_price = ask
        entry_fee = entry_price * qty * maker_fee
        raw_exit = ((entry_price * qty) - entry_fee - target_profit) / (
            qty * (1 + maker_fee)
        )
        exit_price = round_to_tick(raw_exit, tick_size)

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

    if abs(exit_price - entry_price) <= tick_size:
        print(
            json.dumps(
                {
                    "status": "skipped",
                    "message": f"Target profit of ${target_profit} matches or falls below a single asset tick size ({tick_size}). Increase quantity or target_profit.",
                }
            )
        )
        return

    # ----------------------------------------------------------------------
    # 5️⃣  OPTIONAL: Balance check (fails fast)
    # ----------------------------------------------------------------------
    if balance_check:
        try:
            check_margin(api_key, api_secret, symbol, qty, entry_price, maker_fee)
        except RuntimeError as exc:
            print(
                json.dumps(
                    {"status": "error", "stage": "balance_check", "message": str(exc)}
                )
            )
            return

    # ----------------------------------------------------------------------
    # 6️⃣  OPTIONAL: Trailing‑stop handling
    # ----------------------------------------------------------------------
    stop_loss_price = None
    if trailing_stop_pct:
        # trailing_stop_pct is a string like "0.5" → 0.5%
        trail_pct = float(trailing_stop_pct)
        # For a BUY we set a *stop‑loss* a little below entry; for a SELL we set it above.
        if side == "Buy":
            stop_loss_price = round_to_tick(
                entry_price * (1 - trail_pct / 100), tick_size
            )
        else:
            stop_loss_price = round_to_tick(
                entry_price * (1 + trail_pct / 100), tick_size
            )

    # ----------------------------------------------------------------------
    # 7️⃣  Build the native TP/SL order payload
    # ----------------------------------------------------------------------
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
        "tpOrderType": "Limit",
        "tpSize": str(qty),
        "tpTriggerBy": "LastPrice",
    }

    # If a trailing‑stop was requested we also add a stopLoss field.
    if stop_loss_price:
        entry_payload["stopLoss"] = str(stop_loss_price)

    entry_res = send_signed_post("/v5/order/create", entry_payload, api_key, api_secret)

    if entry_res.get("retCode") != 0:
        print(
            json.dumps(
                {"status": "failed", "stage": "execution", "response": entry_res}
            )
        )
        return

    # ----------------------------------------------------------------------
    # 8️⃣  Emit success JSON (the canonical result that aichat/llm‑functions expects)
    # ----------------------------------------------------------------------
    print(
        json.dumps(
            {
                "status": "success",
                "direction": "LONG" if side == "Buy" else "SHORT",
                "metrics": {
                    "momentum": round(momentum, 6),
                    "imbalance": round(imbalance, 2),
                },
                "execution": {
                    "entry_limit_price": entry_price,
                    "take_profit_price": exit_price,
                    "tick_spread_required": round(
                        abs(exit_price - entry_price) / tick_size, 1
                    ),
                    "order_id": entry_res.get("result", {}).get("orderId"),
                    "architecture": "Chained Native TP Order (Automated Risk Lifecycle)",
                    "trailing_stop_applied": bool(stop_loss_price),
                },
            },
            indent=2,
        )
    )


def run(args):
    """Compatibility shim for the argc runner – simply forwards to main()."""
    main(args)


if __name__ == "__main__":
    # ----------------------------------------------------------------------
    # argc‑compatible parsing of “--flag value” style arguments
    # ----------------------------------------------------------------------
    args_dict = {}
    for arg in sys.argv[1:]:
        if arg.startswith("--"):
            k, v = arg.split("=", 1)
            args_dict[k.replace("--", "").replace("-", "_")] = v
    main(args_dict)
