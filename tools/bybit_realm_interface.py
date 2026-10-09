"""
Bybit Trading Tool - Execute any supported trading operation
"""

from typing import Any, Dict, List, Literal, Optional

from bybit_realm import (
    Category,
    OrderSide,
    OrderType,
    PositionIdx,
    TimeInForce,
    TriggerBy,
    _get_dispatcher,
    _require,
)


def run(
    action: Literal[
        "health_check",
        "get_server_time",
        "place_order",
        "place_order_with_sizing",
        "place_conditional_order",
        "amend_order",
        "cancel_order",
        "cancel_all_orders",
        "get_open_orders",
        "get_order_history",
        "get_positions",
        "get_wallet_balance",
        "get_account_info",
        "get_fee_rates",
        "set_leverage",
        "set_trading_stop",
        "get_ticker",
        "get_tickers_bulk",
        "get_orderbook",
        "get_klines",
        "get_recent_trades",
        "get_open_interest",
        "get_liquidations",
        "get_instruments_info",
        "get_spread_analysis",
        "get_technical_analysis",
        "get_market_momentum",
        "get_funding_rate",
        "get_mark_price",
        "get_index_price",
        "calculate_sl_tp",
        "calculate_position_size",
        "calculate_atr_position_size",
        "get_pnl_history",
        "get_pnl_report",
        "batch_orders",
        "iceberg_order",
        "reset_circuit",
        "invalidate_cache",
    ],
    symbol: Optional[str] = None,
    side: Optional[Literal["Buy", "Sell"]] = None,
    qty: Optional[float] = None,
    price: Optional[float] = None,
    order_type: Optional[
        Literal["Limit", "Market", "LimitMaker", "Stop", "StopLimit"]
    ] = None,
    category: Optional[Literal["linear", "inverse", "spot", "option"]] = None,
    order_id: Optional[str] = None,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    trailing_stop: Optional[float] = None,
    reduce_only: Optional[bool] = False,
    time_in_force: Optional[Literal["GTC", "IOC", "FOK", "PostOnly"]] = None,
    position_idx: Optional[int] = None,
    client_oid: Optional[str] = None,
    trigger_price: Optional[float] = None,
    trigger_by: Optional[Literal["LastPrice", "IndexPrice", "MarkPrice"]] = None,
    leverage: Optional[int] = None,
    buy_leverage: Optional[int] = None,
    sell_leverage: Optional[int] = None,
    account_type: Optional[str] = "UNIFIED",
    limit: Optional[int] = 25,
    interval: Optional[str] = "1",
    interval_time: Optional[str] = "5min",
    depth: Optional[int] = 5,
    symbols: Optional[List[str]] = None,
    base_coin: Optional[str] = None,
    status: Optional[str] = None,
    strong_threshold: Optional[float] = 0.20,
    mild_threshold: Optional[float] = 0.08,
    rsi_period: Optional[int] = 14,
    bb_period: Optional[int] = 20,
    bb_std: Optional[float] = 2.0,
    atr_mult: Optional[float] = 1.5,
    sl_pct: Optional[float] = None,
    tp_pct: Optional[float] = None,
    risk_usdt: Optional[float] = None,
    sl_price: Optional[float] = None,
    orders: Optional[List[Dict[str, Any]]] = None,
    slices: Optional[int] = 5,
    delay: Optional[float] = None,
):
    """
    Bybit Trading Tool - Execute any supported trading operation.
    """
    bot = _get_dispatcher()

    try:
        cat = Category(category or "linear")
        tif = TimeInForce(time_in_force or "GTC")
        pidx = PositionIdx(position_idx if position_idx is not None else 0)
        trig = TriggerBy(trigger_by or "LastPrice")

        # Basic action routing
        if action == "health_check":
            return {"status": "ok"}

        elif action == "place_order":
            err = _require(("symbol", symbol), ("side", side), ("qty", qty))
            if err:
                return err
            return bot.place_order(
                symbol=symbol,
                side=OrderSide(side),
                qty=qty,
                price=price,
                order_type=OrderType(order_type or "Limit"),
                category=cat,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reduce_only=reduce_only or False,
                time_in_force=tif,
                position_idx=pidx,
                client_oid=client_oid,
                trailing_stop=trailing_stop,
            )

        # Extend with other actions...
        return {
            "status": "error",
            "msg": f"Action {action} not implemented in interface yet",
        }

    except Exception as exc:
        return {"status": "error", "msg": str(exc), "action": action, "symbol": symbol}
