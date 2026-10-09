#!/usr/bin/env python3
import argparse
import json
from typing import Optional

import bybit_core
import bybit_turso_logger


def run(
    category: str = "linear",
    symbol: str = "BTCUSDT",
    order_id: Optional[str] = None,
    order_link_id: Optional[str] = None,
):
    """Cancel an order on Bybit exchange.
    Args:
        category: Product type (linear/inverse/option/spot)
        symbol: Symbol name
        order_id: Order ID to cancel
        order_link_id: Order link ID to cancel
    """
    params = {"category": category, "symbol": symbol}
    if order_id:
        params["orderId"] = order_id
    if order_link_id:
        params["orderLinkId"] = order_link_id

    resp = bybit_core.api_request(
        "POST", "/v5/order/cancel", params=params, signed=True
    )

    # Log cancellation to Turso
    if resp.get("retCode") == 0:
        bybit_turso_logger.log_event(
            "ORDER_CANCEL",
            {
                "symbol": symbol,
                "details": f"OrderID: {order_id or order_link_id}, Category: {category}, Resp: {resp.get('retMsg')}",
            },
        )

    return resp


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", default="linear")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--order-id")
    parser.add_argument("--order-link-id")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.category, args.symbol, args.order_id, args.order_link_id),
            indent=2,
        )
    )
