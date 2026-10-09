#!/usr/bin/env python3
"""
Custom Bybit trading functions for PnL calculation, signal analysis, and market depth.
"""

import argparse
import json
from typing import Literal


def calculate_tp_pnl(
    entry_price: float,
    take_profit_price: float,
    quantity: float,
    side: Literal["Buy", "Sell"],
    fee_rate: float = 0.001,
    position_size_multiplier: float = 1.0,
    leverage: float = 1.0,
    target_pnl_usdt: float = 0.05,
) -> dict:
    """Calculate take-profit PnL with fees and leverage."""
    entry_notional = entry_price * quantity * position_size_multiplier
    tp_notional = take_profit_price * quantity * position_size_multiplier
    is_long = side.upper() == "BUY"
    raw_pnl = (
        (tp_notional - entry_notional) if is_long else (entry_notional - tp_notional)
    )
    total_fees = (entry_notional + tp_notional) * fee_rate
    net_pnl = raw_pnl - total_fees
    leveraged_net_pnl = net_pnl * leverage
    margin_used = entry_notional / leverage
    roi_percentage = (leveraged_net_pnl / margin_used) * 100 if margin_used != 0 else 0
    target_met = leveraged_net_pnl >= target_pnl_usdt
    return {
        "status": "success",
        "entry_price": entry_price,
        "tp_price": take_profit_price,
        "quantity": quantity,
        "side": side.upper(),
        "leverage": leverage,
        "net_pnl_usdt": round(net_pnl, 4),
        "leveraged_net_pnl_usdt": round(leveraged_net_pnl, 4),
        "roi_percentage": round(roi_percentage, 2),
        "is_profitable": leveraged_net_pnl > 0,
        "target_pnl_usdt": target_pnl_usdt,
        "target_met": target_met,
    }


def get_market_depth_analysis(symbol: str, depth: int = 20) -> dict:
    """Analyze orderbook depth for a symbol."""
    import bybit_core

    data = bybit_core.api_request(
        "GET",
        "/v5/market/orderbook",
        params={"category": "linear", "symbol": symbol, "limit": depth},
        signed=False,
    )
    if data.get("retCode") != 0:
        return {"status": "error", "msg": data.get("retMsg")}

    res = data.get("result", {})
    bids = [[float(p), float(q)] for p, q in res.get("b", [])]
    asks = [[float(p), float(q)] for p, q in res.get("a", [])]

    bid_vol = sum(x[1] for x in bids)
    ask_vol = sum(x[1] for x in asks)
    imbalance = (
        (bid_vol - ask_vol) / (bid_vol + ask_vol) if (bid_vol + ask_vol) > 0 else 0
    )
    spread = (asks[0][0] - bids[0][0]) if asks and bids else 0
    bid_avg = bid_vol / len(bids) if bids else 0
    ask_avg = ask_vol / len(asks) if asks else 0
    bid_walls = [x for x in bids if x[1] > bid_avg * 1.5]
    ask_walls = [x for x in asks if x[1] > ask_avg * 1.5]

    return {
        "status": "success",
        "symbol": symbol,
        "metrics": {
            "imbalance_score": round(imbalance, 4),
            "spread_usdt": round(spread, 8),
            "bid_total_vol": round(bid_vol, 2),
            "ask_total_vol": round(ask_vol, 2),
        },
        "support_levels": [{"price": x[0], "volume": x[1]} for x in bid_walls],
        "resistance_levels": [{"price": x[0], "volume": x[1]} for x in ask_walls],
    }


def analyze_trade_signal(symbol: str) -> dict:
    """Generate trade signal from orderbook imbalance."""
    depth = get_market_depth_analysis(symbol)
    if depth["status"] != "success":
        return depth

    imbalance = depth["metrics"]["imbalance_score"]
    recommendation = "LONG" if imbalance > 0.05 else "SHORT"
    conf_score = 0
    if (recommendation == "LONG" and imbalance > 0.1) or (
        recommendation == "SHORT" and imbalance < -0.1
    ):
        conf_score += 1
    if len(depth["support_levels"]) > 0:
        conf_score += 1

    return {
        "status": "success",
        "symbol": symbol,
        "signal": recommendation,
        "confidence": "HIGH" if conf_score >= 2 else "LOW",
        "depth_metrics": depth["metrics"],
        "key_levels": {
            "support": depth["support_levels"],
            "resistance": depth["resistance_levels"],
        },
    }


def run(action: str, **kwargs) -> dict:
    """Dispatch action to appropriate function."""
    from bybit_realm import BybitRealm

    realm = BybitRealm()
    if action == "pnl":
        return calculate_tp_pnl(
            kwargs.get("entry"),
            kwargs.get("tp"),
            kwargs.get("qty"),
            kwargs.get("side"),
            leverage=kwargs.get("leverage", 1.0),
        )
    elif action == "signal":
        return analyze_trade_signal(kwargs.get("symbol"))
    elif action == "depth":
        return get_market_depth_analysis(kwargs.get("symbol"))
    elif hasattr(realm, action):
        return getattr(realm, action)(**kwargs)
    return {"status": "error", "msg": f"Unknown action: {action}"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", required=True)
    parser.add_argument("--symbol")
    parser.add_argument("--entry", type=float)
    parser.add_argument("--tp", type=float)
    parser.add_argument("--qty", type=float)
    parser.add_argument("--side", choices=["Buy", "Sell"])
    parser.add_argument("--leverage", type=float, default=1.0)
    args, unknown = parser.parse_known_args()
    kwargs = {k: v for k, v in vars(args).items() if v is not None}
    for i in range(0, len(unknown), 2):
        if i + 1 < len(unknown):
            kwargs[unknown[i].lstrip("-")] = unknown[i + 1]
    print(json.dumps(run(args.action, **kwargs), indent=2))
