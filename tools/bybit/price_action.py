"""Price Action & Market Structure engine for the Bybit trading suite.

Pure-logic module: it consumes OHLCV candle lists (Bybit format, newest-first)
and produces structure analysis, signals, and trade plans. Because it is pure,
the same code drives live signals (`BybitRealm`), the backtester, and unit tests.

Candle row format (Bybit v5 kline): [start_ms, open, high, low, close, vol, turnover]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# ── small pure helpers ──────────────────────────────────────────────────────


def _ema_series(values: Sequence[float], period: int) -> List[float]:
    k = 2.0 / (period + 1)
    out = [float(values[0])]
    for v in values[1:]:
        out.append(float(v) * k + out[-1] * (1 - k))
    return out


def _atr_series(highs, lows, closes, period: int = 14) -> List[float]:
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if not trs:
        return []
    out = [sum(trs[:period]) / min(period, len(trs))]
    for i in range(period, len(trs)):
        out.append((out[-1] * (period - 1) + trs[i]) / period)
    # pad so out aligns with closes[1:]
    return [out[0]] * (period - 1) + out


@dataclass
class SwingPoint:
    index: int  # position in the chronological candle list
    price: float
    kind: str  # "high" | "low"


@dataclass
class Signal:
    action: str  # LONG | SHORT | WAIT
    confidence: float  # 0..100
    entry: float
    stop_loss: float
    take_profits: List[float]
    rr: float
    setup: str
    reasons: List[str] = field(default_factory=list)
    filters: List[str] = field(default_factory=list)


class PriceActionEngine:
    """Trend + market-structure analysis for price action trading.

    Terminology:
      * UPTREND   — higher highs and higher lows (HH/HL)
      * DOWNTREND — lower highs and lower lows (LH/LL)
      * RANGE     — no consistent structure
    """

    def __init__(
        self,
        ema_fast: int = 20,
        ema_mid: int = 50,
        ema_slow: int = 200,
        atr_period: int = 14,
        swing_strength: int = 2,
        rsi_period: int = 14,
        adx_period: int = 14,
    ):
        self.ema_fast = ema_fast
        self.ema_mid = ema_mid
        self.ema_slow = ema_slow
        self.atr_period = atr_period
        self.swing_strength = swing_strength
        self.rsi_period = rsi_period
        self.adx_period = adx_period

    # ── data accessors ──────────────────────────────────────────────────────

    @staticmethod
    def to_chronological(klines: Sequence[Sequence]) -> Tuple[List, List, List, List, List]:
        """Convert newest-first klines to chronological lists."""
        chron = list(reversed(list(klines)))
        o = [float(k[1]) for k in chron]
        h = [float(k[2]) for k in chron]
        l = [float(k[3]) for k in chron]
        c = [float(k[4]) for k in chron]
        v = [float(k[5]) for k in chron]
        return o, h, l, c, v

    # ── structure ───────────────────────────────────────────────────────────

    def swing_points(self, klines: Sequence[Sequence]) -> List[SwingPoint]:
        """Detect swing highs/lows using fractal confirmation."""
        o, h, l, c, v = self.to_chronological(klines)
        s = self.swing_strength
        swings: List[SwingPoint] = []
        for i in range(s, len(h) - s):
            window_h = h[i - s : i + s + 1]
            window_l = l[i - s : i + s + 1]
            if h[i] == max(window_h) and h[i] > max(window_h[:s] + window_h[s + 1 :]):
                swings.append(SwingPoint(i, h[i], "high"))
            elif l[i] == min(window_l) and l[i] < min(window_l[:s] + window_l[s + 1 :]):
                swings.append(SwingPoint(i, l[i], "low"))
        return swings

    def market_structure(self, klines: Sequence[Sequence]) -> dict:
        """Classify market structure from recent swing points."""
        swings = self.swing_points(klines)
        if len(swings) < 4:
            return {
                "status": "ok",
                "state": "UNKNOWN",
                "note": "not enough swings",
                "swings": [],
            }
        recent = swings[-6:]
        highs = [s.price for s in recent if s.kind == "high"]
        lows = [s.price for s in recent if s.kind == "low"]
        hh = len(highs) >= 2 and highs[-1] > highs[-2]
        hl = len(lows) >= 2 and lows[-1] > lows[-2]
        lh = len(highs) >= 2 and highs[-1] < highs[-2]
        ll = len(lows) >= 2 and lows[-1] < lows[-2]

        if hh and hl:
            state = "UPTREND"
        elif lh and ll:
            state = "DOWNTREND"
        else:
            state = "RANGE"

        # Slope/EMA fallback: in strong trends fractal swings can be sparse
        # (every candle makes an extreme), so confirm with net change vs ATR.
        o, h, l, c, v = self.to_chronological(klines)
        if state == "RANGE" and len(c) > 40:
            lookback = min(60, len(c) // 3)
            net = c[-1] - c[-lookback]
            atr = self.atr(klines)
            if atr > 0:
                ema_f = _ema_series(c, self.ema_fast)[-1]
                ema_s = _ema_series(c, self.ema_slow)[-1]
                if net > 4 * atr and ema_f > ema_s:
                    state = "UPTREND"
                elif net < -4 * atr and ema_f < ema_s:
                    state = "DOWNTREND"

        # Break of structure: price violating the most recent opposing swing
        bos = None
        last_high = max((s for s in recent if s.kind == "high"), key=lambda s: s.index, default=None)
        last_low = min((s for s in recent if s.kind == "low"), key=lambda s: s.index, default=None)
        if state == "DOWNTREND" and last_high and c[-1] > last_high.price:
            bos = "BULLISH_BOS"
        elif state == "UPTREND" and last_low and c[-1] < last_low.price:
            bos = "BEARISH_BOS"

        return {
            "status": "ok",
            "state": state,
            "bos": bos,
            "swings": [
                {"index": s.index, "price": round(s.price, 6), "kind": s.kind}
                for s in recent
            ],
            "last_high": round(last_high.price, 6) if last_high else None,
            "last_low": round(last_low.price, 6) if last_low else None,
        }

    # ── trend / momentum ────────────────────────────────────────────────────

    def ema_stack(self, klines: Sequence[Sequence]) -> dict:
        o, h, l, c, v = self.to_chronological(klines)
        if len(c) < self.ema_slow:
            return {"status": "error", "msg": "not enough candles for EMA stack"}
        f = _ema_series(c, self.ema_fast)[-1]
        m = _ema_series(c, self.ema_mid)[-1]
        s = _ema_series(c, self.ema_slow)[-1]
        price = c[-1]
        if f > m > s and price > f:
            alignment = "BULLISH"
        elif f < m < s and price < f:
            alignment = "BEARISH"
        elif price > s:
            alignment = "MIXED_BULL"
        else:
            alignment = "MIXED_BEAR"
        return {
            "status": "ok",
            "alignment": alignment,
            "price": round(price, 6),
            f"ema_{self.ema_fast}": round(f, 6),
            f"ema_{self.ema_mid}": round(m, 6),
            f"ema_{self.ema_slow}": round(s, 6),
        }

    def rsi(self, klines: Sequence[Sequence]) -> float:
        o, h, l, c, v = self.to_chronological(klines)
        period = self.rsi_period
        if len(c) < period + 1:
            return 50.0
        diffs = [c[i + 1] - c[i] for i in range(len(c) - 1)]
        gains = [d if d > 0 else 0 for d in diffs]
        losses = [-d if d < 0 else 0 for d in diffs]
        avg_g = sum(gains[:period]) / period
        avg_l = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
        if avg_l == 0:
            return 100.0
        return 100 - 100 / (1 + avg_g / avg_l)

    def adx(self, klines: Sequence[Sequence]) -> dict:
        o, h, l, c, v = self.to_chronological(klines)
        period = self.adx_period
        if len(c) < 2 * period:
            return {"status": "error", "adx": 0.0, "di_plus": 0.0, "di_minus": 0.0}
        trs, pdms, ndms = [], [], []
        for i in range(1, len(c)):
            trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
            up = h[i] - h[i - 1]
            down = l[i - 1] - l[i]
            pdms.append(up if up > down and up > 0 else 0)
            ndms.append(down if down > up and down > 0 else 0)
        # Wilder ADX: average of the last `period` DX values
        dx_series = []
        for i in range(period - 1, len(trs)):
            s_tr = sum(trs[i - period + 1 : i + 1])
            s_pdm = sum(pdms[i - period + 1 : i + 1])
            s_ndm = sum(ndms[i - period + 1 : i + 1])
            di_p = 100 * s_pdm / s_tr if s_tr else 0
            di_n = 100 * s_ndm / s_tr if s_tr else 0
            dx_series.append(
                100 * abs(di_p - di_n) / (di_p + di_n) if (di_p + di_n) else 0
            )
        adx = sum(dx_series[-period:]) / period if dx_series else 0
        s_tr = sum(trs[-period:])
        di_p = 100 * sum(pdms[-period:]) / s_tr if s_tr else 0
        di_n = 100 * sum(ndms[-period:]) / s_tr if s_tr else 0
        return {
            "status": "ok",
            "adx": round(adx, 2),
            "di_plus": round(di_p, 2),
            "di_minus": round(di_n, 2),
        }

    def atr(self, klines: Sequence[Sequence]) -> float:
        o, h, l, c, v = self.to_chronological(klines)
        atr = _atr_series(h, l, c, self.atr_period)
        return atr[-1] if atr else 0.0

    # ── levels ──────────────────────────────────────────────────────────────

    def support_resistance_zones(
        self, klines: Sequence[Sequence], tolerance_atr: float = 0.5
    ) -> dict:
        """Cluster swing levels into S/R zones within a tolerance band."""
        swings = self.swing_points(klines)
        atr = self.atr(klines) or 0
        tol = tolerance_atr * atr
        supports, resistances = [], []
        for s in swings:
            bucket = supports if s.kind == "low" else resistances
            for zone in bucket:
                if abs(s.price - zone["price"]) <= max(tol, 1e-12):
                    zone["touches"] += 1
                    zone["price"] = (zone["price"] * (zone["touches"] - 1) + s.price) / zone["touches"]
                    break
            else:
                bucket.append({"price": s.price, "touches": 1})
        for zone in supports + resistances:
            zone["price"] = round(zone["price"], 6)
        supports.sort(key=lambda z: z["price"], reverse=True)
        resistances.sort(key=lambda z: z["price"])
        return {
            "status": "ok",
            "atr": round(atr, 6),
            "supports": supports[:5],
            "resistances": resistances[:5],
        }

    # ── candle quality ──────────────────────────────────────────────────────

    @staticmethod
    def _candle_shape(k) -> dict:
        o, h, l, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
        rng = h - l
        body = abs(c - o)
        upper = h - max(o, c)
        lower = min(o, c) - l
        return {
            "bullish": c > o,
            "body_pct": body / rng if rng else 0,
            "upper_wick_pct": upper / rng if rng else 0,
            "lower_wick_pct": lower / rng if rng else 0,
        }

    def rejection_candle(self, klines: Sequence[Sequence]) -> Optional[str]:
        """Detect a pin-bar rejection on the most recent completed candle."""
        if len(klines) < 1:
            return None
        shape = self._candle_shape(klines[0])
        if shape["lower_wick_pct"] >= 0.6 and shape["body_pct"] <= 0.35:
            return "BULLISH_REJECTION"
        if shape["upper_wick_pct"] >= 0.6 and shape["body_pct"] <= 0.35:
            return "BEARISH_REJECTION"
        return None

    # ── composite signal ────────────────────────────────────────────────────

    def price_action_signal(
        self,
        klines: Sequence[Sequence],
        min_confidence: float = 55.0,
        rr_min: float = 2.0,
        sl_atr_mult: float = 1.5,
    ) -> Signal:
        """Composite price-action trade signal.

        Combines:
          1. Market structure (HH/HL vs LH/LL)
          2. EMA stack alignment
          3. Momentum filters (RSI extremes, ADX strength)
          4. Optional pullback + rejection-candle trigger
        Returns a Signal with entry/SL/TP ladder and confidence.
        """
        structure = self.market_structure(klines)
        emas = self.ema_stack(klines)
        atr = self.atr(klines) or 0
        o, h, l, c, v = self.to_chronological(klines)
        price = c[-1] if c else 0.0
        rsi_val = self.rsi(klines)
        adx_res = self.adx(klines)
        adx_val = adx_res.get("adx", 0.0)
        rejection = self.rejection_candle(klines)
        zones = self.support_resistance_zones(klines)

        if not price or atr <= 0:
            return Signal("WAIT", 0, price, price, [], 0, "NO_DATA",
                          ["insufficient data"],
                          ["insufficient candles or zero ATR"])

        confidence = 0.0
        reasons: List[str] = []
        filters: List[str] = []
        action = "WAIT"
        setup = "NONE"
        entry = price
        stop = price
        tps: List[float] = []
        rr = 0.0

        struct_state = structure.get("state")
        ema_align = emas.get("alignment")
        bos = structure.get("bos")

        # Direction bias from structure + EMA stack
        bull_points = 0
        if struct_state == "UPTREND":
            bull_points += 2
            reasons.append("structure UPTREND (HH/HL)")
        elif struct_state == "DOWNTREND":
            bull_points -= 2
            reasons.append("structure DOWNTREND (LH/LL)")
        if ema_align == "BULLISH":
            bull_points += 2
            reasons.append("EMA stack bullish aligned")
        elif ema_align == "BEARISH":
            bull_points -= 2
            reasons.append("EMA stack bearish aligned")
        if bos == "BULLISH_BOS":
            bull_points += 1
            reasons.append("bullish break of structure")
        elif bos == "BEARISH_BOS":
            bull_points -= 1
            reasons.append("bearish break of structure")

        # Momentum filters (veto conditions)
        if adx_val >= 20:
            reasons.append(f"ADX {adx_val} confirms trending market")
        else:
            filters.append(f"ADX {adx_val:.1f} < 20 — weak trend")
        # RSI only vetoes when the trend itself is weak: overbought/oversold
        # readings routinely persist in strong trends (that is what momentum
        # looks like), so we never fight a healthy trend with an oscillator.
        if rsi_val >= 75 and adx_val < 25:
            filters.append(f"RSI {rsi_val:.1f} overbought in weak trend — no new longs")
        elif rsi_val <= 25 and adx_val < 25:
            filters.append(f"RSI {rsi_val:.1f} oversold in weak trend — no new shorts")
        elif rsi_val >= 75:
            filters.append(f"RSI {rsi_val:.1f} hot — trim longs, no adds")
        elif rsi_val <= 25:
            filters.append(f"RSI {rsi_val:.1f} cold — trim shorts, no adds")

        long_ok = bull_points >= 3 and adx_val >= 20 and not (rsi_val >= 75 and adx_val < 25)
        short_ok = bull_points <= -3 and adx_val >= 20 and not (rsi_val <= 25 and adx_val < 25)

        # Trigger refinement: pullback + rejection candle adds confidence
        if long_ok:
            action, setup = "LONG", "TREND_CONTINUATION"
            if emas.get("alignment") == "BULLISH" and emas.get(f"ema_{self.ema_fast}", 0):
                pullback = price <= emas[f"ema_{self.ema_fast}"] * 1.005
                if pullback:
                    setup = "PULLBACK_TO_EMA"
                    reasons.append("pullback into EMA zone")
            if rejection == "BULLISH_REJECTION":
                setup = "PULLBACK_REJECTION"
                reasons.append("bullish rejection candle at level")
            # Structure stop (below recent swing low), capped at 3x ATR so the
            # R:R math stays honest; otherwise a far-away swing low produces a
            # stop so wide the TP ladder can never reach the claimed R:R.
            stop = price - atr * sl_atr_mult
            if structure.get("last_low") and structure["last_low"] < price:
                stop = max(structure["last_low"] - atr * 0.2, price - 3 * atr)
            tps = [price + (price - stop) * r for r in (rr_min, rr_min * 1.5, rr_min * 2.5)]
            rr = rr_min
        elif short_ok:
            action, setup = "SHORT", "TREND_CONTINUATION"
            if emas.get("alignment") == "BEARISH" and emas.get(f"ema_{self.ema_fast}", 0):
                if price >= emas[f"ema_{self.ema_fast}"] * 0.995:
                    setup = "PULLBACK_TO_EMA"
                    reasons.append("pullback into EMA zone")
            if rejection == "BEARISH_REJECTION":
                setup = "PULLBACK_REJECTION"
                reasons.append("bearish rejection candle at level")
            stop = price + atr * sl_atr_mult
            if structure.get("last_high") and structure["last_high"] > price:
                stop = min(structure["last_high"] + atr * 0.2, price + 3 * atr)
            tps = [price - (stop - price) * r for r in (rr_min, rr_min * 1.5, rr_min * 2.5)]
            rr = rr_min

        if action != "WAIT":
            confidence = min(95.0, 40 + abs(bull_points) * 10)
            if setup.startswith("PULLBACK"):
                confidence += 10
            if rejection:
                confidence += 5
            if filters:
                confidence -= 8 * len(filters)

        if confidence < min_confidence:
            filters.append(f"confidence {confidence:.0f} below threshold {min_confidence:.0f}")
            return Signal("WAIT", round(confidence, 1), price, stop, tps, rr,
                          setup, reasons, filters)

        return Signal(
            action=action,
            confidence=round(confidence, 1),
            entry=round(entry, 6),
            stop_loss=round(stop, 6),
            take_profits=[round(t, 6) for t in tps],
            rr=round(rr, 2),
            setup=setup,
            reasons=reasons,
            filters=filters,
        )

    # ── trade plan (risk sizing) ────────────────────────────────────────────

    def trade_plan(
        self,
        signal: Signal,
        balance: float,
        risk_pct: float = 1.0,
        leverage: float = 1.0,
        fee_rate: float = 0.00055,
        rr_min: float = 2.0,
    ) -> dict:
        """Size a position from account balance and per-trade risk.

        Risk is always defined by the SL distance first (risk-first sizing).
        """
        if signal.action == "WAIT":
            return {"status": "no_trade", "msg": "No signal — plan withheld"}
        if balance <= 0:
            return {"status": "error", "msg": "Invalid balance"}
        risk_amount = balance * (risk_pct / 100.0)
        sl_distance = abs(signal.entry - signal.stop_loss)
        if sl_distance <= 0:
            return {"status": "error", "msg": "Invalid stop distance"}
        qty = risk_amount / sl_distance
        notional = qty * signal.entry
        margin = notional / max(leverage, 1.0)
        fees_est = fee_rate * notional * 2  # round trip

        if notional > balance:
            return {
                "status": "error",
                "msg": f"Notional {notional:.2f} USDT exceeds balance {balance:.2f} USDT",
                "suggested_leverage": round(notional / balance, 2),
            }

        # Expected values per R
        r_unit = risk_amount
        return {
            "status": "ok",
            "action": signal.action,
            "confidence": signal.confidence,
            "setup": signal.setup,
            "entry": signal.entry,
            "stop_loss": signal.stop_loss,
            "take_profits": signal.take_profits,
            "sl_distance": round(sl_distance, 6),
            "sl_distance_pct": round(sl_distance / signal.entry * 100, 3),
            "rr": signal.rr,
            "risk_pct": risk_pct,
            "risk_amount": round(risk_amount, 2),
            "qty": round(qty, 8),
            "notional": round(notional, 2),
            "margin": round(margin, 2),
            "est_fees": round(fees_est, 3),
            "leverage": leverage,
            "target_pnl_1r": round(r_unit - fees_est, 2),
            "reasons": signal.reasons,
            "filters": signal.filters,
        }


def analyze_price_action(
    klines: Sequence[Sequence],
    min_confidence: float = 55.0,
    rr_min: float = 2.0,
) -> dict:
    """One-shot convenience wrapper returning the full PA analysis dict."""
    eng = PriceActionEngine()
    structure = eng.market_structure(klines)
    emas = eng.ema_stack(klines)
    atr = eng.atr(klines)
    rsi = eng.rsi(klines)
    adx = eng.adx(klines)
    zones = eng.support_resistance_zones(klines)
    signal = eng.price_action_signal(klines, min_confidence, rr_min)
    return {
        "status": "ok",
        "structure": structure,
        "ema_stack": emas,
        "atr": round(atr, 6),
        "rsi": round(rsi, 2),
        "adx": adx,
        "zones": zones,
        "rejection_candle": eng.rejection_candle(klines),
        "signal": {
            "action": signal.action,
            "confidence": signal.confidence,
            "setup": signal.setup,
            "entry": signal.entry,
            "stop_loss": signal.stop_loss,
            "take_profits": signal.take_profits,
            "rr": signal.rr,
            "reasons": signal.reasons,
            "filters": signal.filters,
        },
    }
