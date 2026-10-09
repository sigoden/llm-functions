# @describe Place Bybit order
# @option --category <linear|inverse> Product type
# @option --symbol <SYMBOL> Symbol
# @option --side <Buy|Sell> Side
# @option --order-type <Market|Limit> Order type
# @option --qty <QTY> Quantity
#!/usr/bin/env python3
import argparse
import json

import bybit_core
import bybit_turso_logger


def run(
    category,
    symbol,
    side,
    order_type,
    qty,
    price=None,
    time_in_force="GTC",
    take_profit=None,
    stop_loss=None,
    reduce_only=False,
):
    params = {
        "category": category,
        "symbol": symbol,
        "side": side,
        "orderType": order_type,
        "qty": qty,
        "timeInForce": time_in_force,
    }
    if price:
        params["price"] = price
    if take_profit:
        params["takeProfit"] = take_profit
    if stop_loss:
        params["stopLoss"] = stop_loss
    if reduce_only:
        params["reduceOnly"] = True

    resp = bybit_core.api_request(
        "POST", "/v5/order/create", params=params, signed=True
    )

    # Log successful trades to Turso
    if resp.get("retCode") == 0:
        bybit_turso_logger.log_event(
            "TRADE_OPEN",
            {
                "symbol": symbol,
                "side": side,
                "price": price or "MARKET",
                "qty": qty,
                "details": f"Category: {category}, OrderType: {order_type}, Resp: {resp.get('retMsg')}",
            },
        )

    return json.dumps(resp, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", default="linear")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--side", default="Buy")
    parser.add_argument("--order-type", default="Market")
    parser.add_argument("--qty")
    parser.add_argument("--price")
    parser.add_argument("--time-in-force", default="GTC")
    parser.add_argument("--take-profit")
    parser.add_argument("--stop-loss")
    parser.add_argument("--reduce-only", action="store_true")
    args = parser.parse_args()
    print(
        run(
            args.category,
            args.symbol,
            args.side,
            args.order_type,
            args.qty,
            args.price,
            args.time_in_force,
            args.take_profit,
            args.stop_loss,
            args.reduce_only,
        )
    )
