#!/usr/bin/env python3
"""Backtester for the Bybit price-action strategy.

@describe Backtest the price-action trend strategy over Bybit klines.
@option --symbol <TEXT>      Trading pair (e.g. BTCUSDT).
@option --interval <TEXT>    Kline timeframe: 1, 5, 15, 60, 240, D (default: 60).
@option --limit <NUM>        Number of candles to fetch (default: 1000).
@option --risk-pct <NUM>     Risk per trade, % of balance (default: 1.0).
@option --rr <NUM>           Reward:risk multiple (default: 2.0).
@option --sl-atr <NUM>       Stop-loss distance in ATRs (default: 1.5).
@option --start-balance <NUM> Starting balance in USDT (default: 1000).
@option --fee-rate <NUM>     Taker fee rate per side (default: 0.00055).
@option --min-confidence <NUM> Minimum signal confidence (default: 55).
@flag --json                Output raw JSON.
"""

from __future__ import annotations

import argparse
import json
from typing import Dict, List, Optional, Sequence, Tuple

from tools.bybit.price_action import PriceActionEngine, Signal


# ── indicator precompute ────────────────────────────────────────────────────


def _precompute(chron, engine: PriceActionEngine) -> dict:
    from tools.bybit.price_action import _atr_series, _ema_series

    o = [float(k[1]) for k in chron]
    h = [float(k[2]) for k in chron]
    l = [float(k[3]) for k in chron]
    c = [float(k[4]) for k in chron]
    v = [float(k[5]) for k in chron]
    n = len(c)
    ema_f = _ema_series(c, engine.ema_fast)
    ema_m = _ema_series(c, engine.ema_mid)
    ema_s = _ema_series(c, engine.ema_slow)
    atr = _atr_series(h, l, c, engine.atr_period)
    rsi_series = []
    period = engine.rsi_period
    diffs = [c[i + 1] - c[i] for i in range(n - 1)]
    gains = [d if d > 0 else 0 for d in diffs]
    losses = [-d if d < 0 else 0 for d in diffs]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    rsi_series = [50.0] * period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        rsi_series.append(100 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l))

    # Wilder ADX: DX series, then ADX = average of the last `period` DX values
    trs, pdms, ndms = [], [], []
    for i in range(1, n):
        trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
        up = h[i] - h[i - 1]
        down = l[i - 1] - l[i]
        pdms.append(up if up > down and up > 0 else 0)
        ndms.append(down if down > up and down > 0 else 0)
    p = engine.adx_period
    dx_series = [0.0] * (n - 1)
    for i in range(p - 1, n - 1):
        s_tr = sum(trs[i - p + 1 : i + 1])
        s_pdm = sum(pdms[i - p + 1 : i + 1])
        s_ndm = sum(ndms[i - p + 1 : i + 1])
        di_p = 100 * s_pdm / s_tr if s_tr else 0
        di_n = 100 * s_ndm / s_tr if s_tr else 0
        dx_series[i] = 100 * abs(di_p - di_n) / (di_p + di_n) if (di_p + di_n) else 0
    adx_series = [0.0] * n
    for i in range(2 * p - 1, n):
        adx_series[i] = sum(dx_series[i - p : i]) / p

    return {
        "o": o, "h": h, "l": l, "c": c, "v": v,
        "ema_f": ema_f, "ema_m": ema_m, "ema_s": ema_s,
        "atr": atr, "rsi": rsi_series, "adx": adx_series,
    }


def _signal_at(chron, i: int, pre: dict, engine: PriceActionEngine,
               min_confidence: float, rr_min: float, sl_mult: float = 1.5) -> Optional[Signal]:
    """Compute a signal using data up to bar i (inclusive)."""
    c = pre["c"]
    price = c[i]
    atr = pre["atr"][i] if i < len(pre["atr"]) else 0.0
    if atr <= 0:
        return None

    # structure from a rolling window
    window = list(reversed(chron[max(0, i - 120) : i + 1]))
    structure = engine.market_structure(window)
    swings = structure.get("swings", [])
    last_high = structure.get("last_high")
    last_low = structure.get("last_low")

    # EMA alignment
    f, m, s = pre["ema_f"][i], pre["ema_m"][i], pre["ema_s"][i]
    if f > m > s and price > f:
        ema_bull = 2
    elif f < m < s and price < f:
        ema_bull = -2
    else:
        ema_bull = 0

    struct_bull = 2 if structure.get("state") == "UPTREND" else (
        -2 if structure.get("state") == "DOWNTREND" else 0)
    bos = structure.get("bos")
    bos_bull = 1 if bos == "BULLISH_BOS" else (-1 if bos == "BEARISH_BOS" else 0)
    bull_points = struct_bull + ema_bull + bos_bull

    rsi_val = pre["rsi"][i] if i < len(pre["rsi"]) else 50.0
    adx_val = pre["adx"][i] if i < len(pre["adx"]) else 0.0

    # RSI only vetoes when the trend itself is weak (mirrors PriceActionEngine)
    rsi_veto_long = rsi_val >= 75 and adx_val < 25
    rsi_veto_short = rsi_val <= 25 and adx_val < 25

    long_ok = bull_points >= 3 and adx_val >= 20 and not rsi_veto_long
    short_ok = bull_points <= -3 and adx_val >= 20 and not rsi_veto_short

    action = "WAIT"
    if long_ok:
        action = "LONG"
    elif short_ok:
        action = "SHORT"
    if action == "WAIT":
        return None

    # rejection candle on last completed bar
    shape_ok = False
    if i >= 1:
        k = chron[i]
        o_, h_, l_, c_ = float(k[1]), float(k[2]), float(k[3]), float(k[4])
        rng = h_ - l_
        if rng > 0:
            body = abs(c_ - o_)
            upper = h_ - max(o_, c_)
            lower = min(o_, c_) - l_
            if action == "LONG" and lower / rng >= 0.6 and body / rng <= 0.35:
                shape_ok = True
            if action == "SHORT" and upper / rng >= 0.6 and body / rng <= 0.35:
                shape_ok = True

    confidence = 40 + abs(bull_points) * 10 + (5 if shape_ok else 0)
    if confidence < min_confidence:
        return None

    if action == "LONG":
        stop = price - atr * sl_mult
        if last_low and last_low < price:
            stop = max(last_low - atr * 0.2, price - 3 * atr)
        dist = price - stop
        tps = [price + dist]  # 1R target (2R target set after partial fill)
    else:
        stop = price + atr * sl_mult
        if last_high and last_high > price:
            stop = min(last_high + atr * 0.2, price + 3 * atr)
        dist = stop - price
        tps = [price - dist]  # 1R target
    return Signal(action, round(confidence, 1), round(price, 6),
                  round(stop, 6), [round(t, 6) for t in tps],
                  rr_min, "TREND_CONTINUATION")


def run_backtest(
    klines: Sequence[Sequence],
    risk_pct: float = 1.0,
    rr: float = 2.0,
    sl_atr: float = 1.5,
    start_balance: float = 1000.0,
    fee_rate: float = 0.00055,
    min_confidence: float = 55.0,
    warmup: int = 220,
    sl_atr_mult: Optional[float] = None,
    engine: Optional[PriceActionEngine] = None,
) -> dict:
    """Run the price-action strategy over a kline series (newest-first input).

    Returns summary stats and a per-trade log. Conservative assumptions:
    * if SL and TP are both touched within one bar, the SL is counted first;
    * taker fees are charged on every fill (entry, exit, and each scale-out);
    * the equity curve is marked to the bar close (unrealized PnL included);
    * notional per trade is capped at the current equity (leverage 1).
    """
    engine = engine or PriceActionEngine()
    sl_mult = sl_atr_mult if sl_atr_mult is not None else sl_atr

    chron = list(reversed(list(klines)))
    pre = _precompute(chron, engine)
    c, h, l = pre["c"], pre["h"], pre["l"]

    realized = 0.0  # cumulative realized PnL (net of fees)
    equity_curve: List[float] = [float(start_balance)]
    trades: List[dict] = []
    pos = None

    def equity(p, mark_price: float) -> float:
        """start + realized + unrealized PnL of the open position."""
        base = float(start_balance) + realized
        if p is None:
            return base
        return base + p["side"] * p["qty"] * (mark_price - p["entry"])

    for i in range(warmup, len(chron) - 1):
        if pos is None:
            sig = _signal_at(chron, i, pre, engine, min_confidence, rr, sl_mult)
            if sig is None:
                equity_curve.append(equity_curve[-1])
                continue
            side = 1 if sig.action == "LONG" else -1
            entry = float(sig.entry)
            stop = float(sig.stop_loss)
            # first scale-out target at 1R; 2R target is set after the partial fill
            tp = float(sig.take_profits[0])
            dist = abs(entry - stop)
            if dist <= 0:
                equity_curve.append(equity_curve[-1])
                continue
            # scale-out ladder: first target at 1R (relative to this stop)
            eq = equity(None, entry)
            risk_amount = eq * (risk_pct / 100.0)
            qty = risk_amount / dist
            notional = qty * entry
            if notional > eq:
                qty = eq / entry
                risk_amount = qty * dist
            if qty <= 0:
                equity_curve.append(equity_curve[-1])
                continue
            realized -= fee_rate * notional  # entry fee
            pos = {
                "side": side,
                "entry": entry,
                "qty": qty,
                "stop": stop,
                "tp": tp,
                "partial_closed": False,
                "bars_held": 0,
                "entry_i": i,
                "risk_amount": risk_amount,
                "entry_notional": notional,
                "partial_pnl": 0.0,
                "dist": dist,
            }
            equity_curve.append(equity(pos, entry))
            continue

        # manage open position against bar i+1 (the bar after signal)
        k = chron[i + 1]
        hi, lo = float(k[2]), float(k[3])
        exit_price = None
        exit_reason = None
        pos["bars_held"] += 1

        if pos["side"] == 1:
            if lo <= pos["stop"]:
                exit_price, exit_reason = pos["stop"], "STOP_LOSS"
            elif not pos["partial_closed"] and hi >= pos["tp"]:  # 1R target
                # close half at 1R, move SL to breakeven
                half = pos["qty"] / 2
                part = (pos["tp"] - pos["entry"]) * half - fee_rate * half * pos["tp"]
                realized += part
                pos["partial_pnl"] += part
                pos["qty"] -= half
                pos["partial_closed"] = True
                pos["stop"] = pos["entry"]
                pos["tp"] = pos["entry"] + pos["dist"] * 2  # 2R target
            elif pos["partial_closed"] and hi >= pos["tp"]:
                exit_price, exit_reason = pos["tp"], "TAKE_PROFIT_2R"
            elif pos["partial_closed"] and lo <= pos["stop"]:
                exit_price, exit_reason = pos["stop"], "BREAKEVEN"
        else:
            if hi >= pos["stop"]:
                exit_price, exit_reason = pos["stop"], "STOP_LOSS"
            elif not pos["partial_closed"] and lo <= pos["tp"]:
                half = pos["qty"] / 2
                part = (pos["entry"] - pos["tp"]) * half - fee_rate * half * pos["tp"]
                realized += part
                pos["partial_pnl"] += part
                pos["qty"] -= half
                pos["partial_closed"] = True
                pos["stop"] = pos["entry"]
                pos["tp"] = pos["entry"] - pos["dist"] * 2
            elif pos["partial_closed"] and lo <= pos["tp"]:
                exit_price, exit_reason = pos["tp"], "TAKE_PROFIT_2R"
            elif pos["partial_closed"] and hi >= pos["stop"]:
                exit_price, exit_reason = pos["stop"], "BREAKEVEN"

        if exit_price is not None:
            pnl = (exit_price - pos["entry"]) * pos["side"] * pos["qty"]
            pnl -= fee_rate * pos["qty"] * (pos["entry"] + exit_price)
            realized += pnl
            total_pnl = pnl + pos["partial_pnl"]
            trades.append({
                "entry": pos["entry"],
                "exit": round(exit_price, 6),
                "side": "LONG" if pos["side"] == 1 else "SHORT",
                "reason": exit_reason,
                "bars_held": pos["bars_held"],
                "pnl_usdt": round(total_pnl, 4),
                "r_multiple": round(total_pnl / pos["risk_amount"], 2) if pos["risk_amount"] else 0,
            })
            pos = None
        equity_curve.append(equity(pos, float(k[4])))

    # close any open position at the last close
    if pos is not None:
        last_close = c[-1]
        pnl = (last_close - pos["entry"]) * pos["side"] * pos["qty"]
        pnl -= fee_rate * pos["qty"] * (pos["entry"] + last_close)
        realized += pnl
        total_pnl = pnl + pos["partial_pnl"]
        trades.append({
            "entry": pos["entry"],
            "exit": round(last_close, 6),
            "side": "LONG" if pos["side"] == 1 else "SHORT",
            "reason": "END_OF_DATA",
            "bars_held": pos["bars_held"],
            "pnl_usdt": round(total_pnl, 4),
            "r_multiple": round(total_pnl / pos["risk_amount"], 2) if pos["risk_amount"] else 0,
        })
        equity_curve.append(equity(None, last_close))

    return _summarize(trades, equity_curve, start_balance)


def _summarize(trades: List[dict], equity_curve: List[float], start_balance: float) -> dict:
    wins = [t for t in trades if t["pnl_usdt"] > 0]
    losses = [t for t in trades if t["pnl_usdt"] <= 0]
    gross_profit = sum(t["pnl_usdt"] for t in wins)
    gross_loss = abs(sum(t["pnl_usdt"] for t in losses))
    net = gross_profit - gross_loss

    # max drawdown on the equity curve
    peak = -1e18
    max_dd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)

    total_r = sum(t["r_multiple"] for t in trades)
    return {
        "status": "ok",
        "summary": {
            "start_balance": start_balance,
            "final_balance": round(equity_curve[-1], 2),
            "net_pnl": round(net, 2),
            "return_pct": round((equity_curve[-1] - start_balance) / start_balance * 100, 2),
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0,
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else (
                round(gross_profit, 2) if gross_profit > 0 else 0),
            "expectancy_r": round(total_r / len(trades), 3) if trades else 0,
            "max_drawdown_pct": round(max_dd * 100, 2),
            "avg_bars_held": round(sum(t["bars_held"] for t in trades) / len(trades), 1) if trades else 0,
        },
        "trades": trades[-50:],
    }


def run(
    symbol: str = "BTCUSDT",
    interval: str = "60",
    limit: int = 1000,
    risk_pct: float = 1.0,
    rr: float = 2.0,
    sl_atr: float = 1.5,
    start_balance: float = 1000.0,
    fee_rate: float = 0.00055,
    min_confidence: float = 55.0,
    klines: Optional[Sequence[Sequence]] = None,
    **kwargs,
) -> dict:
    """Unified entry point. Fetch klines from the realm client unless `klines`
    is supplied (used by the in-process dispatcher and tests)."""
    if klines is None:
        from tools.bybit.terminal import BybitRealm

        bot = BybitRealm()
        try:
            data = bot.get_klines(symbol, interval, limit)
            klines = data.get("list", data.get("result", {}).get("list", []))
        finally:
            bot.close()
        if not klines:
            return {"status": "error", "msg": f"No klines returned for {symbol}"}
    return run_backtest(
        klines,
        risk_pct=risk_pct,
        rr=rr,
        sl_atr=sl_atr,
        start_balance=start_balance,
        fee_rate=fee_rate,
        min_confidence=min_confidence,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the Bybit price-action strategy")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="60")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--risk-pct", type=float, default=1.0)
    parser.add_argument("--rr", type=float, default=2.0)
    parser.add_argument("--sl-atr", type=float, default=1.5)
    parser.add_argument("--start-balance", type=float, default=1000.0)
    parser.add_argument("--fee-rate", type=float, default=0.00055)
    parser.add_argument("--min-confidence", type=float, default=55.0)
    args = parser.parse_args()

    result = run(
        symbol=args.symbol,
        interval=args.interval,
        limit=args.limit,
        risk_pct=args.risk_pct,
        rr=args.rr,
        sl_atr=args.sl_atr,
        start_balance=args.start_balance,
        fee_rate=args.fee_rate,
        min_confidence=args.min_confidence,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
